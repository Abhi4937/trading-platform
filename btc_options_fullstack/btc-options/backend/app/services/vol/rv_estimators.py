"""
Realized volatility estimators.

All functions take a pandas DataFrame with columns:
  - open, high, low, close (and optionally volume)

All return annualized volatility (decimal, e.g. 0.31 = 31%).

Estimators:
  1. close_to_close       — textbook standard, uses only Close
  2. close_open           — single-session directional
  3. parkinson            — range-based, ~5x efficient vs CC
  4. garman_klass         — full OHLC, assumes no drift
  5. rogers_satchell      — full OHLC, drift-robust

References:
  Parkinson (1980), Garman & Klass (1980), Rogers & Satchell (1991)

Vendored verbatim from rv_engine/rv_estimators.py (only the config import path
changed). Formulas verified correct.
"""

import numpy as np
import pandas as pd

from .constants import DAYS_PER_YEAR


def _validate_ohlc(df: pd.DataFrame) -> None:
    """Ensure required columns are present and clean."""
    required = {"open", "high", "low", "close"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing OHLC columns: {missing}")
    if len(df) < 2:
        raise ValueError("Need at least 2 candles for RV calculation")


def close_to_close(df: pd.DataFrame, annualize: bool = True) -> float:
    """
    Close-to-Close RV. The textbook standard.

    sigma_CC = sqrt( (1/(n-1)) * sum( ln(C_t / C_{t-1})^2 ) )

    This is what most papers and IV models reference.
    """
    _validate_ohlc(df)
    log_returns = np.log(df["close"] / df["close"].shift(1)).dropna()
    if len(log_returns) < 2:
        return np.nan
    variance = log_returns.var(ddof=1)
    daily_vol = np.sqrt(variance)
    return daily_vol * np.sqrt(DAYS_PER_YEAR) if annualize else daily_vol


def close_open(df: pd.DataFrame, annualize: bool = True) -> float:
    """
    Close-Open RV. Single-session directional vol.

    sigma_CO = sqrt( (1/n) * sum( ln(C_t / O_t)^2 ) )

    Matches single-session traders (e.g. Fri night → Sat morning hold).
    """
    _validate_ohlc(df)
    log_returns = np.log(df["close"] / df["open"]).dropna()
    if len(log_returns) < 1:
        return np.nan
    variance = (log_returns ** 2).mean()
    daily_vol = np.sqrt(variance)
    return daily_vol * np.sqrt(DAYS_PER_YEAR) if annualize else daily_vol


def parkinson(df: pd.DataFrame, annualize: bool = True) -> float:
    """
    Parkinson (1980) range-based RV.

    sigma_P = sqrt( (1/(4*n*ln(2))) * sum( ln(H_t / L_t)^2 ) )

    ~5x more statistically efficient than Close-to-Close.
    Captures intraday swings. Slightly underestimates if drift exists.
    Best for short lookbacks where CC is noisy.
    """
    _validate_ohlc(df)
    hl_log_sq = (np.log(df["high"] / df["low"]) ** 2).dropna()
    if len(hl_log_sq) < 1:
        return np.nan
    variance = hl_log_sq.mean() / (4 * np.log(2))
    daily_vol = np.sqrt(variance)
    return daily_vol * np.sqrt(DAYS_PER_YEAR) if annualize else daily_vol


def garman_klass(df: pd.DataFrame, annualize: bool = True) -> float:
    """
    Garman-Klass (1980) RV. Uses all of OHLC.

    sigma_GK^2 = (1/n) * sum( 0.5 * ln(H/L)^2 - (2*ln(2) - 1) * ln(C/O)^2 )

    ~8x more efficient than CC. Assumes zero drift — biased when trending.
    The standard 'best single number' for vol estimation.
    """
    _validate_ohlc(df)
    hl_term = 0.5 * np.log(df["high"] / df["low"]) ** 2
    co_term = (2 * np.log(2) - 1) * np.log(df["close"] / df["open"]) ** 2
    daily_var = (hl_term - co_term).dropna().mean()
    if pd.isna(daily_var) or daily_var <= 0:
        return np.nan
    daily_vol = np.sqrt(daily_var)
    return daily_vol * np.sqrt(DAYS_PER_YEAR) if annualize else daily_vol


def rogers_satchell(df: pd.DataFrame, annualize: bool = True) -> float:
    """
    Rogers-Satchell (1991) RV. Drift-robust.

    sigma_RS^2 = (1/n) * sum(
        ln(H/C) * ln(H/O) + ln(L/C) * ln(L/O)
    )

    Unlike GK, doesn't assume zero drift. Correctly handles trending markets.
    When RS >> GK, drift is suppressing GK's estimate → strong trend present.
    """
    _validate_ohlc(df)
    log_hc = np.log(df["high"] / df["close"])
    log_ho = np.log(df["high"] / df["open"])
    log_lc = np.log(df["low"] / df["close"])
    log_lo = np.log(df["low"] / df["open"])

    rs_term = log_hc * log_ho + log_lc * log_lo
    daily_var = rs_term.dropna().mean()
    if pd.isna(daily_var) or daily_var <= 0:
        return np.nan
    daily_vol = np.sqrt(daily_var)
    return daily_vol * np.sqrt(DAYS_PER_YEAR) if annualize else daily_vol


def compute_all_estimators(df: pd.DataFrame, annualize: bool = True) -> dict:
    """Compute all five estimators on the given OHLC frame."""
    return {
        "close_to_close": close_to_close(df, annualize),
        "close_open": close_open(df, annualize),
        "parkinson": parkinson(df, annualize),
        "garman_klass": garman_klass(df, annualize),
        "rogers_satchell": rogers_satchell(df, annualize),
    }


def daily_stats(df: pd.DataFrame) -> dict:
    """
    Compute per-day stats for the OHLC frame:
      - Range % (H-L)/Open
      - Close-Open % |C-O|/Open
      - Trend efficiency |C-O|/(H-L)
    Returns medians, stdevs, and the raw series.
    """
    _validate_ohlc(df)
    range_pct = (df["high"] - df["low"]) / df["open"]
    co_pct = (df["close"] - df["open"]).abs() / df["open"]
    # Avoid division by zero
    trend_eff = (df["close"] - df["open"]).abs() / (df["high"] - df["low"]).replace(0, np.nan)

    return {
        "range_pct_series": range_pct,
        "co_pct_series": co_pct,
        "trend_eff_series": trend_eff,
        "range_median": range_pct.median(),
        "range_stdev": range_pct.std(),
        "range_mean": range_pct.mean(),
        "co_median": co_pct.median(),
        "co_stdev": co_pct.std(),
        "co_mean": co_pct.mean(),
        "trend_eff_median": trend_eff.median(),
        "n_days": len(df),
    }


def annualize_period_vol(vol: float, period_hours: float) -> float:
    """
    Convert a vol estimate measured over a specific period into annualized vol.

    e.g. sigma over 12h -> sigma * sqrt( (365*24) / 12 )
    """
    return vol * np.sqrt((DAYS_PER_YEAR * 24) / period_hours)


def deannualize_to_period(annual_vol: float, period_hours: float) -> float:
    """Inverse — annual vol → expected move over `period_hours`."""
    return annual_vol / np.sqrt((DAYS_PER_YEAR * 24) / period_hours)
