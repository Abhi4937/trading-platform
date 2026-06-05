"""Vol Analytics orchestrator for the Historical Dashboard.

Builds the point-in-time snapshot shown in the collapsible "Vol Analytics" panel:
ATM IV for the selected expiry at the simulated timestamp, the 5-estimator RV term
structure, IV/RV ratio grid, regime read, term-structure shape, Fri-Sat weekend-window
stats, stop-loss candidates, and the gamma-vs-theta verdict.

Pure-historical (parquet only) → works on any session slot. The lifetime IV-vs-RV
mini-chart shown alongside this is served separately by the existing
`/historical/atm-iv-series` endpoint.

Data plumbing is reused from app.api.historical (imported lazily to avoid a circular
import) and greeks from app.core.greeks. The vol math lives in app.services.vol.*.
"""

import logging
import math

import numpy as np
import pandas as pd

from app.services.vol.constants import (
    LOOKBACK_WINDOWS,
    PRIMARY_LOOKBACK,
    SL_LOOKBACK,
    FRI_SAT_WEEKS,
    DEFAULT_HOLD_HOURS,
)
from app.services.vol.rv_estimators import compute_all_estimators, daily_stats
from app.services.vol.regime import detect_regime
from app.services.vol.ratio_analyzer import (
    compute_ratio_grid,
    classify_signal,
    term_structure_shape,
)
from app.services.vol.fri_sat_filter import compute_fri_sat_stats
from app.services.vol.sl_engine import compute_sl_candidates, minimum_reasonable_sl_dollars
from app.services.vol.greeks_ext import gamma_theta_ratio

logger = logging.getLogger(__name__)

_EST_KEYS = {
    "cc": "close_to_close",
    "co": "close_open",
    "parkinson": "parkinson",
    "gk": "garman_klass",
    "rs": "rogers_satchell",
}


def _n(x, decimals: int = 2):
    """NaN/None-safe rounder → None when not finite (keeps JSON valid)."""
    if x is None:
        return None
    try:
        xf = float(x)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(xf):
        return None
    return round(xf, decimals)


def _empty(expiry: str, timestamp: int, atm: dict, reason: str) -> dict:
    return {
        "available": False,
        "reason": reason,
        "expiry": expiry,
        "timestamp": int(timestamp),
        "header": {
            "spot": _n(atm.get("spot"), 2) or 0.0,
            "atm_strike": int(atm.get("atm_strike") or 0),
            "atm_iv_call": _n(atm.get("atm_iv_call")) or 0.0,
            "atm_iv_put": _n(atm.get("atm_iv_put")) or 0.0,
            "atm_iv_avg": _n(atm.get("atm_iv_avg")) or 0.0,
            "dte_hours": _n(atm.get("dte_hours")) or 0.0,
            "primary_lookback": PRIMARY_LOOKBACK,
            "primary_estimator": "close_to_close",
            "primary_ratio": 0.0,
            "signal": {"level": "unknown", "action": "data unavailable", "color": "gray"},
            "regime_label": "unknown",
            "ts_used": int(atm.get("ts_used") or timestamp),
            "snapped": bool(atm.get("snapped")),
        },
        "rv_grid": [],
        "ratio_grid": [],
        "regime": None,
        "term_structure": None,
        "fri_sat": None,
        "sl": None,
        "gamma_theta": None,
    }


def build_vol_analytics(expiry: str, timestamp: int) -> dict:
    """Return the Vol Analytics snapshot for `expiry` (YYYY-MM-DD) at `timestamp`."""
    # Lazy import to break the historical.py <-> vol_analytics.py import cycle.
    from app.api.historical import compute_atm_iv, _bucketed_spot_ohlc
    from app.core.greeks import compute_greeks

    timestamp = int(timestamp)
    atm = compute_atm_iv(expiry, timestamp)

    if not atm or atm.get("atm_iv_avg", 0) <= 0:
        return _empty(expiry, timestamp, atm or {}, "No ATM IV for this expiry at this time")

    spot = float(atm["spot"])
    atm_strike = int(atm["atm_strike"])
    T = float(atm["T"])
    iv_avg_pct = float(atm["atm_iv_avg"])
    iv_decimal = iv_avg_pct / 100.0
    # The ATM IV may have snapped to the nearest available bar; anchor the RV
    # history and Fri-Sat windows to that same moment so everything is consistent.
    ref_ts = int(atm.get("ts_used") or timestamp)

    # --- Daily spot OHLC: ~40 days before ref_ts (covers the 30d lookback +1) ---
    daily_start = ref_ts - 41 * 86400
    daily = _bucketed_spot_ohlc(daily_start, ref_ts, "1d")
    if daily is None or len(daily) < 2:
        return _empty(expiry, timestamp, atm, "Insufficient daily spot history")

    # --- RV/IV ratio grid (daily) ---
    grid = compute_ratio_grid(iv_decimal, daily, LOOKBACK_WINDOWS)

    # --- Regime read on the most recent PRIMARY_LOOKBACK days ---
    regime_window = daily.tail(PRIMARY_LOOKBACK + 1)
    try:
        rv_for_regime = compute_all_estimators(regime_window)
    except ValueError:
        rv_for_regime = compute_all_estimators(daily)
    regime = detect_regime(rv_for_regime)
    rec = regime.recommended_estimator  # e.g. "close_to_close"

    # --- Primary ratio + signal (recommended estimator at the primary lookback) ---
    primary_ratio = np.nan
    primary_rv_decimal = np.nan
    if not grid.empty:
        lb = PRIMARY_LOOKBACK if PRIMARY_LOOKBACK in grid.index else grid.index[-1]
        primary_ratio = grid.loc[lb, f"{rec}_ratio"]
        primary_rv_decimal = grid.loc[lb, f"{rec}_rv"]
    signal = classify_signal(primary_ratio)
    ts_shape = term_structure_shape(grid, "close_to_close")

    # --- Fri-Sat weekend-window stats: hourly OHLC ~ (FRI_SAT_WEEKS+1) weeks back ---
    hourly_start = ref_ts - (FRI_SAT_WEEKS + 1) * 7 * 86400
    hourly = _bucketed_spot_ohlc(hourly_start, ref_ts, "1h")
    fri_sat = None
    if hourly is not None and len(hourly) > 6:
        hourly_fs = hourly.rename(columns={"time": "timestamp"}).copy()
        hourly_fs["timestamp"] = pd.to_datetime(hourly_fs["timestamp"], unit="s", utc=True)
        ref_time = pd.to_datetime(ref_ts, unit="s", utc=True)
        fs = compute_fri_sat_stats(hourly_fs, n_weeks=FRI_SAT_WEEKS,
                                   hold_hours=DEFAULT_HOLD_HOURS, ref_time=ref_time)
        if fs.get("window_count", 0) > 0:
            ann_co = fs.get("annualized_co_vol")
            window_iv_rv = (iv_decimal / ann_co) if (ann_co and ann_co > 0) else None
            med_co = fs.get("median_co_pct")
            fri_sat = {
                "window_count": int(fs["window_count"]),
                "median_range_pct": _n(fs.get("median_range_pct"), 4),
                "median_co_pct": _n(med_co, 4),
                "median_move_usd": _n((med_co * spot) if med_co is not None else None, 2),
                "annualized_range_vol": _n((fs.get("annualized_range_vol") or 0) * 100, 2),
                "annualized_co_vol": _n((ann_co or 0) * 100, 2),
                "window_iv_rv": _n(window_iv_rv, 2),
                "hold_hours": float(fs.get("hold_hours", DEFAULT_HOLD_HOURS)),
            }

    # --- Stop-loss candidates from the short daily range window ---
    sl = None
    try:
        sl_window = daily.tail(max(SL_LOOKBACK, 2))
        ds = daily_stats(sl_window)
        sl_cand = compute_sl_candidates(ds["range_median"], ds["range_stdev"], spot,
                                        hold_hours=DEFAULT_HOLD_HOURS)
        # Min-reasonable SL: prefer the Fri-Sat true sigma, else the primary RV.
        ann_co_decimal = None
        if fri_sat and fri_sat.get("annualized_co_vol"):
            ann_co_decimal = fri_sat["annualized_co_vol"] / 100.0
        elif primary_rv_decimal and math.isfinite(primary_rv_decimal):
            ann_co_decimal = float(primary_rv_decimal)
        min_sl = (minimum_reasonable_sl_dollars(ann_co_decimal, spot,
                                                hold_hours=DEFAULT_HOLD_HOURS)
                  if ann_co_decimal else {})
        sl = {
            "tight": {"pct": _n(sl_cand["tight"]["pct"], 5), "dollars": _n(sl_cand["tight"]["dollars"], 2)},
            "moderate": {"pct": _n(sl_cand["moderate"]["pct"], 5), "dollars": _n(sl_cand["moderate"]["dollars"], 2)},
            "conservative": {"pct": _n(sl_cand["conservative"]["pct"], 5), "dollars": _n(sl_cand["conservative"]["dollars"], 2)},
            "one_sigma_pct": _n(min_sl.get("1_sigma_pct"), 5),
            "one_sigma_dollars": _n(min_sl.get("1_sigma_dollars"), 2),
            "min_reasonable_sl_pct": _n(min_sl.get("min_reasonable_sl_pct"), 5),
            "min_reasonable_sl_dollars": _n(min_sl.get("min_reasonable_sl_dollars"), 2),
            "hold_hours": float(DEFAULT_HOLD_HOURS),
        }
    except (ValueError, KeyError) as e:
        logger.warning(f"vol_analytics SL block failed: {e}")

    # --- Gamma vs Theta (uses the platform's own greeks for one code path) ---
    gamma_theta = None
    sigma_realized = float(primary_rv_decimal) if (primary_rv_decimal and math.isfinite(primary_rv_decimal)) else None
    if sigma_realized and sigma_realized > 0:
        g = compute_greeks(spot, atm_strike, T, 0.0, iv_decimal, "call")
        gt = gamma_theta_ratio(spot, g.gamma, g.theta, sigma_realized)
        gamma_theta = {
            "gamma": _n(gt["gamma"], 8),
            "theta_per_day": _n(gt["theta_per_day"], 4),
            "sigma_realized": _n(sigma_realized * 100, 2),
            "expected_daily_move_1sd": _n(gt["expected_daily_move_1sd"], 2),
            "gamma_pnl_per_day_1sd": _n(gt["gamma_pnl_per_day_1sd"], 4),
            "theta_pnl_per_day": _n(gt["theta_pnl_per_day"], 4),
            "ratio": _n(gt["gamma_theta_ratio"], 4),
            "verdict": gt["verdict"],
            "breakeven_move_per_day": _n(gt["breakeven_move_per_day"], 2),
        }

    # --- Assemble grids (RV in %, ratios as-is) ---
    rv_grid_rows, ratio_grid_rows = [], []
    if not grid.empty:
        for lb in grid.index:
            rv_row = {"lookback": int(lb)}
            ratio_row = {"lookback": int(lb)}
            for short, full in _EST_KEYS.items():
                rv_val = grid.loc[lb, f"{full}_rv"]
                ratio_val = grid.loc[lb, f"{full}_ratio"]
                rv_row[short] = _n((rv_val * 100) if rv_val is not None else None, 2)
                ratio_row[short] = _n(ratio_val, 2)
            rv_grid_rows.append(rv_row)
            ratio_grid_rows.append(ratio_row)

    header = {
        "spot": _n(spot, 2),
        "atm_strike": atm_strike,
        "atm_iv_call": _n(atm.get("atm_iv_call")) or 0.0,
        "atm_iv_put": _n(atm.get("atm_iv_put")) or 0.0,
        "atm_iv_avg": _n(iv_avg_pct),
        "dte_hours": _n(atm.get("dte_hours")),
        "primary_lookback": PRIMARY_LOOKBACK,
        "primary_estimator": rec,
        "primary_ratio": _n(primary_ratio) or 0.0,
        "signal": {"level": signal["level"], "action": signal["action"], "color": signal["color"]},
        "regime_label": regime.chop_label,
        "ts_used": ref_ts,
        "snapped": bool(atm.get("snapped")),
    }

    return {
        "available": True,
        "reason": None,
        "expiry": expiry,
        "timestamp": timestamp,
        "header": header,
        "rv_grid": rv_grid_rows,
        "ratio_grid": ratio_grid_rows,
        "regime": {
            "chop_ratio": _n(regime.chop_ratio),
            "trend_intensity": _n(regime.trend_intensity, 4),
            "chop_label": regime.chop_label,
            "trend_label": regime.trend_label,
            "recommended_estimator": regime.recommended_estimator,
            "rationale": regime.rationale,
        },
        "term_structure": {
            "shape": ts_shape.get("shape", "unknown"),
            "short_rv": _n((ts_shape.get("short_rv") or 0) * 100, 2) if ts_shape.get("short_rv") is not None else None,
            "long_rv": _n((ts_shape.get("long_rv") or 0) * 100, 2) if ts_shape.get("long_rv") is not None else None,
            "diff_pct": _n(ts_shape.get("diff_pct"), 4),
            "interpretation": ts_shape.get("interpretation", ""),
        },
        "fri_sat": fri_sat,
        "sl": sl,
        "gamma_theta": gamma_theta,
    }
