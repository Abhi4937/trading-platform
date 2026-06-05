"""
Stop-loss placement engine.

Computes 3 SL distances (Tight, Moderate, Conservative) based on
range statistics from the short rolling daily window, scaled to the
user's intended hold duration.

  Tight:        median range
  Moderate:     median + 1 * stdev
  Conservative: median + 2 * stdev

All scaled by sqrt(hold_hours / 24) for sub-daily horizons.

Vendored from rv_engine/sl_engine.py (only the config import path changed).
"""

import numpy as np

from .constants import (
    SL_TIGHT_MULT,
    SL_MODERATE_MULT,
    SL_CONSERVATIVE_MULT,
    DEFAULT_HOLD_HOURS,
    DAYS_PER_YEAR,
)


def compute_sl_candidates(
    range_median_pct: float,
    range_stdev_pct: float,
    spot: float,
    hold_hours: float = DEFAULT_HOLD_HOURS,
) -> dict:
    """
    Returns Tight / Moderate / Conservative SL distances.

    Scaling: daily range stats → hold-duration range via sqrt(t) scaling.
    """
    scale = np.sqrt(hold_hours / 24.0)

    tight_pct = (range_median_pct + SL_TIGHT_MULT * range_stdev_pct) * scale
    moderate_pct = (range_median_pct + SL_MODERATE_MULT * range_stdev_pct) * scale
    conservative_pct = (range_median_pct + SL_CONSERVATIVE_MULT * range_stdev_pct) * scale

    return {
        "hold_hours": hold_hours,
        "scale_factor": scale,
        "tight": {
            "pct": tight_pct,
            "dollars": tight_pct * spot,
        },
        "moderate": {
            "pct": moderate_pct,
            "dollars": moderate_pct * spot,
        },
        "conservative": {
            "pct": conservative_pct,
            "dollars": conservative_pct * spot,
        },
    }


def expected_time_to_hit_days(sl_pct: float, daily_co_vol_pct: float) -> float:
    """
    Expected time (in days) for SL to be hit under random walk assumption.

    T_expected = (SL_distance / sigma_daily)^2
    """
    if daily_co_vol_pct <= 0:
        return float("inf")
    return (sl_pct / daily_co_vol_pct) ** 2


def minimum_reasonable_sl_dollars(
    annualized_co_vol: float,
    spot: float,
    hold_hours: float = DEFAULT_HOLD_HOURS,
    safety_multiple: float = 1.5,
) -> dict:
    """
    Compute the minimum sensible SL distance for the hold period.

    Below this, noise will almost certainly stop you out.
    """
    period_vol_pct = annualized_co_vol / np.sqrt((DAYS_PER_YEAR * 24) / hold_hours)
    one_sigma_dollars = period_vol_pct * spot
    min_sl_pct = period_vol_pct * safety_multiple

    return {
        "1_sigma_pct": period_vol_pct,
        "1_sigma_dollars": one_sigma_dollars,
        "min_reasonable_sl_pct": min_sl_pct,
        "min_reasonable_sl_dollars": min_sl_pct * spot,
        "safety_multiple": safety_multiple,
    }
