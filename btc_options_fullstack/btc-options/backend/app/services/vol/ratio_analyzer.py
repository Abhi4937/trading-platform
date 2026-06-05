"""
IV/RV ratio analyzer.

Computes:
  - Multi-lookback IV/RV ratios using all estimators
  - Percentile rank of current ratio vs rolling history
  - Signal classification (Rich / Fair / Cheap)

Vendored from rv_engine/ratio_analyzer.py (only import paths changed).
"""

import numpy as np
import pandas as pd
from typing import Optional

from .constants import (
    LOOKBACK_WINDOWS,
    RATIO_RICH,
    RATIO_FAIR_UPPER,
    RATIO_FAIR_LOWER,
    RATIO_CHEAP,
    PERCENTILE_RICH,
    PERCENTILE_CHEAP,
)
from .rv_estimators import compute_all_estimators


def compute_ratio_grid(
    iv_decimal: float,
    daily_df: pd.DataFrame,
    lookbacks: list = LOOKBACK_WINDOWS,
) -> pd.DataFrame:
    """
    Compute IV / RV for each (lookback, estimator) pair.

    Returns DataFrame:
      index   = lookback window (days)
      columns = {estimator}_rv and {estimator}_ratio
    """
    results = []
    for window in lookbacks:
        if len(daily_df) < window + 1:
            continue
        sub = daily_df.tail(window + 1)  # +1 for CC differencing
        rv_dict = compute_all_estimators(sub)
        row = {"lookback": window}
        for est, rv in rv_dict.items():
            row[est + "_rv"] = rv
            row[est + "_ratio"] = (iv_decimal / rv) if rv and rv > 0 else np.nan
        results.append(row)

    if not results:
        return pd.DataFrame()
    return pd.DataFrame(results).set_index("lookback")


def percentile_rank(current_value: float, history_series: pd.Series) -> float:
    """
    What percentile does `current_value` occupy in `history_series`?
    Returns 0–100.
    """
    if history_series.empty:
        return np.nan
    sorted_hist = history_series.sort_values().values
    rank = np.searchsorted(sorted_hist, current_value, side="right")
    return (rank / len(sorted_hist)) * 100


def classify_signal(ratio: float, percentile: Optional[float] = None) -> dict:
    """
    Classify the IV/RV ratio into a trading signal.
    """
    if ratio is None or np.isnan(ratio):
        return {"level": "unknown", "action": "data unavailable", "color": "gray", "percentile_note": ""}

    if ratio >= RATIO_RICH:
        level = "rich"
        action = "Strong sell-premium signal"
        color = "red"
    elif ratio >= RATIO_FAIR_UPPER:
        level = "mildly_rich"
        action = "Mild edge for premium sellers"
        color = "orange"
    elif ratio >= RATIO_FAIR_LOWER:
        level = "fair"
        action = "No edge — stand aside or wait"
        color = "yellow"
    elif ratio >= RATIO_CHEAP:
        level = "mildly_cheap"
        action = "Mild edge for premium buyers"
        color = "light_green"
    else:
        level = "cheap"
        action = "Strong buy-premium signal"
        color = "green"

    # Augment with percentile context if available
    note = ""
    if percentile is not None and not np.isnan(percentile):
        if percentile >= PERCENTILE_RICH:
            note = f"Percentile {percentile:.0f}%: relatively RICH vs recent history"
        elif percentile <= PERCENTILE_CHEAP:
            note = f"Percentile {percentile:.0f}%: relatively CHEAP vs recent history"
        else:
            note = f"Percentile {percentile:.0f}%: in normal range"

    return {
        "level": level,
        "action": action,
        "color": color,
        "percentile_note": note,
    }


def term_structure_shape(ratio_df: pd.DataFrame, estimator: str = "close_to_close") -> dict:
    """
    Classify the term structure of RV across lookbacks.

      Inverted (short >> long) → vol expanding
      Contango (short << long) → vol compressing
      Flat                      → stable regime
    """
    col = f"{estimator}_rv"
    if ratio_df is None or ratio_df.empty or col not in ratio_df.columns:
        return {"shape": "unknown"}

    rvs = ratio_df[col].dropna()
    if len(rvs) < 2:
        return {"shape": "insufficient_data"}

    short_rv = rvs.iloc[0]    # shortest lookback (e.g., 4d)
    long_rv = rvs.iloc[-1]    # longest lookback (e.g., 30d)

    if long_rv <= 0:
        return {"shape": "unknown"}

    diff_pct = (short_rv - long_rv) / long_rv

    if diff_pct > 0.15:
        shape = "inverted"
        interpretation = "VOL EXPANDING — recent days more volatile than baseline. Possible regime change up."
    elif diff_pct < -0.15:
        shape = "contango"
        interpretation = "VOL COMPRESSING — recent days calmer than baseline. Premium-selling environment improving."
    elif abs(diff_pct) <= 0.05:
        shape = "flat"
        interpretation = "STABLE REGIME — vol consistent across windows."
    else:
        shape = "mild_slope"
        interpretation = "MILD SLOPE — small change in vol regime."

    return {
        "shape": shape,
        "short_rv": short_rv,
        "long_rv": long_rv,
        "diff_pct": diff_pct,
        "interpretation": interpretation,
    }
