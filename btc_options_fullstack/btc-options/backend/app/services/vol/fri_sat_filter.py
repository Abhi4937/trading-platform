"""
Friday-Saturday regime filter.

Computes RV specifically for the trade window (Fri evening → Sat morning),
isolated from general weekday vol. This often reveals a structurally
DIFFERENT vol character than Mon-Thu data shows.

Method:
  1. Take hourly BTC candles for the last N weeks (relative to `ref_time`)
  2. For each week, extract the Fri-21:30-IST → Sat-09:30-IST window
     (= Fri 16:00 UTC → Sat 04:00 UTC, ~12 hours)
  3. Compute per-window: range, |close - open|
  4. Take median and stdev across all windows (for the "typical $ move" display)
  5. Annualize the trade-window sigma from the RMS of window returns (a true σ).

Two deliberate deviations from rv_engine/fri_sat_filter.py:
  * `extract_fri_sat_windows` takes an explicit `ref_time` anchor — the simulated
    timestamp — instead of `pd.Timestamp.now()`. The panel runs on historical
    data, so anchoring to the real wall clock would return empty windows.
  * `annualized_co_vol` / `annualized_range_vol` are computed from the RMS of the
    window-return series (sqrt(mean(x²)) * annualizer), NOT the median * annualizer.
    The median understates a true sigma by ~1.5x and is not comparable to IV or to
    the `close_open` estimator; the RMS is the genuine 1-window σ.
"""

from datetime import timezone
import numpy as np
import pandas as pd

from .constants import (
    FRI_SAT_WEEKS,
    DEFAULT_ENTRY_HOUR_UTC,
    DEFAULT_EXIT_HOUR_UTC,
    DEFAULT_HOLD_HOURS,
    DAYS_PER_YEAR,
)


def extract_fri_sat_windows(
    hourly_df: pd.DataFrame,
    n_weeks: int = FRI_SAT_WEEKS,
    entry_hour_utc: int = DEFAULT_ENTRY_HOUR_UTC,
    exit_hour_utc: int = DEFAULT_EXIT_HOUR_UTC,
    ref_time: pd.Timestamp = None,
) -> pd.DataFrame:
    """
    Given hourly OHLC candles, extract Fri-Sat windows looking back from `ref_time`.

    Returns DataFrame with one row per week:
      week_of, entry_time, exit_time, open, high, low, close, n_candles,
      range_pct, co_pct
    """
    if hourly_df.empty:
        return pd.DataFrame()

    df = hourly_df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.set_index("timestamp").sort_index()

    windows = []
    # Anchor weeks to the simulated timestamp, not the live wall clock.
    if ref_time is None:
        now = pd.Timestamp.now(tz=timezone.utc)
    else:
        now = pd.Timestamp(ref_time)
        if now.tzinfo is None:
            now = now.tz_localize(timezone.utc)

    for week_offset in range(n_weeks):
        # Compute the Friday for this week
        target_date = now - pd.Timedelta(days=week_offset * 7)
        # weekday: Mon=0 ... Fri=4 ... Sun=6
        days_back_to_friday = (target_date.weekday() - 4) % 7
        # If today is Friday and week_offset == 0, we may not have a complete window yet;
        # roll back another week in that case.
        if days_back_to_friday == 0 and target_date.hour < exit_hour_utc + 24:
            target_date = target_date - pd.Timedelta(days=7)
        else:
            target_date = target_date - pd.Timedelta(days=days_back_to_friday)

        # Entry: Friday at entry_hour_utc
        entry_dt = target_date.replace(
            hour=entry_hour_utc, minute=0, second=0, microsecond=0
        )
        # Exit: entry + hold hours
        exit_dt = entry_dt + pd.Timedelta(hours=DEFAULT_HOLD_HOURS)

        window = df.loc[entry_dt:exit_dt]
        if len(window) < 6:  # need at least 6 hourly candles for valid window
            continue

        try:
            open_p = float(window.iloc[0]["open"])
            close_p = float(window.iloc[-1]["close"])
            high_p = float(window["high"].max())
            low_p = float(window["low"].min())

            range_pct = (high_p - low_p) / open_p
            co_pct = abs(close_p - open_p) / open_p

            windows.append({
                "week_of": target_date.date(),
                "entry_time": entry_dt,
                "exit_time": exit_dt,
                "open": open_p,
                "high": high_p,
                "low": low_p,
                "close": close_p,
                "n_candles": len(window),
                "range_pct": range_pct,
                "co_pct": co_pct,
            })
        except (IndexError, KeyError, ValueError):
            continue

    if not windows:
        return pd.DataFrame()
    return pd.DataFrame(windows).sort_values("entry_time").reset_index(drop=True)


def _rms(series: pd.Series) -> float:
    """Root-mean-square of a series — the true 1-window sigma."""
    s = series.dropna()
    if len(s) == 0:
        return np.nan
    return float(np.sqrt((s ** 2).mean()))


def compute_fri_sat_stats(
    hourly_df: pd.DataFrame,
    n_weeks: int = FRI_SAT_WEEKS,
    hold_hours: int = DEFAULT_HOLD_HOURS,
    ref_time: pd.Timestamp = None,
) -> dict:
    """
    Compute the trade-window-specific stats.

    Returns medians/stdevs of range_pct & co_pct (for display), plus
    annualized_range_vol / annualized_co_vol computed from the RMS of the
    window-return series (true sigma — see module docstring).
    """
    windows = extract_fri_sat_windows(hourly_df, n_weeks=n_weeks, ref_time=ref_time)

    if windows.empty:
        return {
            "median_range_pct": np.nan,
            "stdev_range_pct": np.nan,
            "median_co_pct": np.nan,
            "stdev_co_pct": np.nan,
            "annualized_range_vol": np.nan,
            "annualized_co_vol": np.nan,
            "window_count": 0,
            "raw_windows": windows,
            "hold_hours": hold_hours,
        }

    # Annualization factor for the trade window
    annualizer = np.sqrt((DAYS_PER_YEAR * 24) / hold_hours)

    # True 1-window sigmas from RMS of returns (NOT the median — see docstring).
    rms_range = _rms(windows["range_pct"])
    rms_co = _rms(windows["co_pct"])

    return {
        "median_range_pct": windows["range_pct"].median(),
        "stdev_range_pct": windows["range_pct"].std(),
        "mean_range_pct": windows["range_pct"].mean(),
        "median_co_pct": windows["co_pct"].median(),
        "stdev_co_pct": windows["co_pct"].std(),
        "mean_co_pct": windows["co_pct"].mean(),
        # Annualized from RMS of window returns → comparable to IV / close_open.
        "annualized_range_vol": rms_range * annualizer,
        "annualized_co_vol": rms_co * annualizer,
        "window_count": len(windows),
        "raw_windows": windows,
        "hold_hours": hold_hours,
        "annualizer": annualizer,
    }
