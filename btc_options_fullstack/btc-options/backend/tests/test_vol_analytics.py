"""Smoke/unit tests for the vendored vol engine (app/services/vol/*).

Adapted from rv_engine/test_engine.py to the platform's vendored package +
platform greeks. Runs offline on synthetic OHLC — no parquet/API needed.

Validates:
  - RV estimators produce sensible numbers
  - Regime detection classifies and recommends an estimator
  - IV/RV ratio grid is well-formed; term-structure & signal thresholds work
  - Gamma-vs-theta (fed by app.core.greeks) gives ratio < 1 when RV << IV
  - The Fri-Sat fix: annualized window vol is the RMS-based true sigma, which
    exceeds the original median-based number on the same windows
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from app.services.vol.rv_estimators import compute_all_estimators
from app.services.vol.regime import detect_regime
from app.services.vol.ratio_analyzer import (
    compute_ratio_grid,
    term_structure_shape,
    classify_signal,
)
from app.services.vol.fri_sat_filter import compute_fri_sat_stats, extract_fri_sat_windows, _rms
from app.services.vol.greeks_ext import gamma_theta_ratio
from app.services.vol.constants import DAYS_PER_YEAR, DEFAULT_HOLD_HOURS
from app.core.greeks import compute_greeks


def make_synthetic_ohlc(n_days=30, annual_vol=0.30, drift=0.0, chop_factor=1.0,
                        starting_price=60000, seed=42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    daily_vol = annual_vol / math.sqrt(DAYS_PER_YEAR)
    rows, price = [], starting_price
    for d in range(n_days):
        open_p = price
        close_p = open_p * math.exp(rng.normal(drift / DAYS_PER_YEAR, daily_vol))
        wick = open_p * daily_vol * chop_factor
        high_p = max(open_p, close_p) + abs(rng.normal(0, 1)) * wick
        low_p = min(open_p, close_p) - abs(rng.normal(0, 1)) * wick
        rows.append({
            "timestamp": datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=d),
            "open": open_p, "high": high_p, "low": low_p, "close": close_p, "volume": 0,
        })
        price = close_p
    return pd.DataFrame(rows)


def make_synthetic_hourly(n_weeks=13, annual_vol=0.50, starting_price=60000,
                          ref_time=None, seed=7) -> pd.DataFrame:
    """Continuous hourly OHLC ending at ref_time (tz-aware), random walk."""
    if ref_time is None:
        ref_time = pd.Timestamp("2026-03-04 12:00", tz="UTC")  # a Wednesday
    rng = np.random.default_rng(seed)
    hourly_vol = annual_vol / math.sqrt(DAYS_PER_YEAR * 24)
    n_hours = n_weeks * 7 * 24
    start = ref_time - pd.Timedelta(hours=n_hours)
    rows, price = [], starting_price
    for h in range(n_hours):
        ts = start + pd.Timedelta(hours=h)
        open_p = price
        close_p = open_p * math.exp(rng.normal(0, hourly_vol))
        wick = open_p * hourly_vol
        high_p = max(open_p, close_p) + abs(rng.normal(0, 1)) * wick
        low_p = min(open_p, close_p) - abs(rng.normal(0, 1)) * wick
        rows.append({"timestamp": ts, "open": open_p, "high": high_p,
                     "low": low_p, "close": close_p, "volume": 0})
        price = close_p
    return pd.DataFrame(rows)


def test_rv_estimators_in_range():
    rv = compute_all_estimators(make_synthetic_ohlc(n_days=30, annual_vol=0.30))
    for name, val in rv.items():
        assert 0.10 < val < 0.60, f"{name}={val:.4f} out of plausible range"


def test_chop_ratio_responds():
    calm = compute_all_estimators(make_synthetic_ohlc(30, 0.30, chop_factor=1.0, seed=11))
    chop = compute_all_estimators(make_synthetic_ohlc(30, 0.30, chop_factor=4.0, seed=11))
    assert chop["parkinson"] / chop["close_open"] > calm["parkinson"] / calm["close_open"]


def test_regime_classification():
    rv = compute_all_estimators(make_synthetic_ohlc(30, 0.30, chop_factor=4.0, seed=99))
    reg = detect_regime(rv)
    assert reg.recommended_estimator in {
        "close_to_close", "close_open", "parkinson", "garman_klass", "rogers_satchell"}
    assert reg.chop_label in {"chop", "trend", "mixed", "unknown"}
    assert isinstance(reg.rationale, str) and reg.rationale


def test_ratio_grid_well_formed():
    df = make_synthetic_ohlc(n_days=60, annual_vol=0.28, seed=123)
    grid = compute_ratio_grid(0.31, df, lookbacks=[4, 7, 14, 30])
    assert not grid.empty
    assert list(grid.index) == [4, 7, 14, 30]
    for est in ["close_to_close", "parkinson", "rogers_satchell"]:
        assert f"{est}_rv" in grid.columns and f"{est}_ratio" in grid.columns
        # ratio = iv / rv must be positive and finite where rv > 0
        assert (grid[f"{est}_ratio"].dropna() > 0).all()


def test_term_structure_contango():
    df = pd.DataFrame({"close_to_close_rv": [0.18, 0.22, 0.26, 0.30]}, index=[4, 7, 14, 30])
    assert term_structure_shape(df, "close_to_close")["shape"] == "contango"


def test_signal_thresholds():
    cases = [(1.8, "rich"), (1.4, "mildly_rich"), (1.0, "fair"),
             (0.85, "mildly_cheap"), (0.6, "cheap")]
    for ratio, expected in cases:
        assert classify_signal(ratio)["level"] == expected


def test_gamma_theta_theta_wins_when_rv_below_iv():
    """Fed by the platform's own greeks: ratio < 1 when RV << IV."""
    spot, strike, T, iv, rv = 60000, 60000, 2 / 365, 0.40, 0.20
    g = compute_greeks(spot, strike, T, 0.0, iv, "call")
    gt = gamma_theta_ratio(spot, g.gamma, g.theta, rv)
    assert g.gamma > 0 and g.theta < 0           # sane greeks
    assert gt["gamma_theta_ratio"] < 1.0
    assert "THETA WINS" in gt["verdict"]


def test_gamma_theta_gamma_wins_when_rv_above_iv():
    spot, strike, T, iv, rv = 60000, 60000, 2 / 365, 0.20, 0.80
    g = compute_greeks(spot, strike, T, 0.0, iv, "call")
    gt = gamma_theta_ratio(spot, g.gamma, g.theta, rv)
    assert gt["gamma_theta_ratio"] > 1.0
    assert "GAMMA WINS" in gt["verdict"]


def test_fri_sat_windows_extracted_with_ref_time():
    """The ref_time anchor must yield weekend windows on historical data."""
    ref = pd.Timestamp("2026-03-04 12:00", tz="UTC")
    hourly = make_synthetic_hourly(n_weeks=13, ref_time=ref, seed=7)
    windows = extract_fri_sat_windows(hourly, n_weeks=12, ref_time=ref)
    assert len(windows) >= 6, "expected several Fri-Sat windows from 13 weeks of data"


def test_fri_sat_rms_exceeds_median_annualization():
    """The fix: annualized_co_vol uses RMS (true sigma) > the old median number."""
    ref = pd.Timestamp("2026-03-04 12:00", tz="UTC")
    hourly = make_synthetic_hourly(n_weeks=13, ref_time=ref, seed=7)
    stats = compute_fri_sat_stats(hourly, n_weeks=12, hold_hours=DEFAULT_HOLD_HOURS, ref_time=ref)
    assert stats["window_count"] >= 6

    raw = stats["raw_windows"]
    annualizer = stats["annualizer"]
    old_median_based = raw["co_pct"].median() * annualizer   # the original (understated) number
    new_rms_based = stats["annualized_co_vol"]                # the fixed number
    assert math.isfinite(new_rms_based) and new_rms_based > 0
    # RMS of right-skewed |returns| strictly exceeds the median-based annualization.
    assert new_rms_based > old_median_based
    # And it should equal RMS(co_pct) * annualizer by construction.
    assert new_rms_based == pytest.approx(_rms(raw["co_pct"]) * annualizer, rel=1e-9)
