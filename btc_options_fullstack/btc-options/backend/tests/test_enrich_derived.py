"""Unit tests for Module 3 derived metrics + pattern detection."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from app.analytics.enrich_derived import (
    PATTERN_A_DELTA_24H,
    PATTERN_A_IVP_4H,
    PATTERN_B_IVP_4H,
    PATTERN_B_SPOT_RET_1D,
    PATTERN_C_DELTA_48H,
    PATTERN_C_IVP_4H,
    PATTERN_D_ADX_4H,
    add_expected_move,
    add_pattern,
    add_vol_of_vol,
    add_vrp_family,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _synthetic_df(n_rows: int, freq_seconds: int = 300) -> pd.DataFrame:
    """Build a small 5m DataFrame with the columns needed downstream."""
    start_unix = 1_700_000_000  # ~2023-11-14
    ts = np.arange(start_unix, start_unix + n_rows * freq_seconds, freq_seconds, dtype="int64")
    return pd.DataFrame({
        "timestamp_unix": ts,
        "close": np.full(n_rows, 100_000.0),
        "rv_7d":  np.full(n_rows, 35.0),     # %  (annualized)
        "rv_14d": np.full(n_rows, 38.0),
        "rv_30d": np.full(n_rows, 40.0),
        "atm_iv_7d":  np.full(n_rows, 0.45), # decimal
        "atm_iv_14d": np.full(n_rows, 0.42),
        "atm_iv_30d": np.full(n_rows, 0.40),
        "atm_iv_60d": np.full(n_rows, 0.39),
        "ivp_4h": np.full(n_rows, 50.0),
        "spot_ret_1d": np.full(n_rows, 0.0),
        "adx_14_4h": np.full(n_rows, 15.0),
    })


# ── VRP family ────────────────────────────────────────────────────────────────

def test_vrp_spread_uses_decimal_rv():
    df = _synthetic_df(5)
    df = add_vrp_family(df)
    # iv_rv_spread_7d = 0.45 - 35/100 = 0.45 - 0.35 = 0.10
    assert all(abs(df["iv_rv_spread_7d"] - 0.10) < 1e-9)
    # iv_rv_ratio_7d = 0.45 / 0.35 ≈ 1.2857
    assert all(abs(df["iv_rv_ratio_7d"] - (0.45 / 0.35)) < 1e-9)


def test_vrp_spread_for_30d():
    df = _synthetic_df(5)
    df = add_vrp_family(df)
    # iv_rv_spread_30d = 0.40 - 40/100 = 0.0
    assert all(abs(df["iv_rv_spread_30d"]) < 1e-9)


def test_vrp_pct_short_window_returns_nan():
    """Without 90 days of history, vrp_pct_X should be NaN."""
    df = _synthetic_df(50)
    df = add_vrp_family(df)
    # 90d window = 25920 5m bars; 50 rows is far below → all NaN
    assert df["vrp_pct_7d"].isna().all()


# ── Expected move ─────────────────────────────────────────────────────────────

def test_expected_move_7d():
    """spot=100k, atm_iv_7d=0.50 → 1σ ≈ 100000 × 0.50 × √(7/365) ≈ $6925."""
    df = _synthetic_df(3)
    df["atm_iv_7d"] = 0.50
    df = add_expected_move(df)
    expected = 100000 * 0.50 * math.sqrt(7 / 365)
    assert all(abs(df["expected_move_1sigma_7d"] - expected) < 1)
    # 2σ = 2× 1σ
    assert all(abs(df["expected_move_2sigma_7d"] - 2 * expected) < 1)


def test_expected_move_30d():
    df = _synthetic_df(1)
    df["close"] = 100000.0
    df["atm_iv_30d"] = 0.40
    df = add_expected_move(df)
    expected = 100000 * 0.40 * math.sqrt(30 / 365)
    assert abs(df["expected_move_1sigma_30d"].iloc[0] - expected) < 1


# ── Pattern detection ─────────────────────────────────────────────────────────

def test_pattern_a_fresh_spike():
    df = _synthetic_df(600)  # need >576 for delta_48h
    # Mid-range row (index 580): set conditions for A
    df.loc[580:, "ivp_4h"] = 75.0  # > 70
    df.loc[579, "ivp_4h"] = 75.0   # avoid NaN at delta calc
    # Make the 24h delta strongly positive: shift 288 bars back to a low value
    df.loc[580 - 288:580 - 288 + 1, "ivp_4h"] = 50.0  # 24h ago = 50 → delta = +25
    df = add_pattern(df)
    assert df.loc[580, "pattern"] == "A"


def test_pattern_b_post_crash():
    df = _synthetic_df(600)
    df.loc[580, "ivp_4h"] = 70.0
    df.loc[580, "spot_ret_1d"] = -0.05  # -5% drop
    # Ensure A doesn't fire: keep delta_24h flat
    df.loc[580 - 288, "ivp_4h"] = 70.0
    df = add_pattern(df)
    assert df.loc[580, "pattern"] == "B"


def test_pattern_c_stale():
    df = _synthetic_df(600)
    # Low IVP, ~zero delta
    df.loc[580, "ivp_4h"] = 50.0
    df.loc[580 - 576, "ivp_4h"] = 49.5  # delta_48h = +0.5 < 3
    df = add_pattern(df)
    assert df.loc[580, "pattern"] == "C"


def test_pattern_d_active_trend():
    df = _synthetic_df(600)
    df.loc[580, "ivp_4h"] = 50.0  # not low enough for C, not high enough for A/B
    df.loc[580, "adx_14_4h"] = 30.0  # > 25
    # Ensure delta_48h doesn't trigger C: make 48h ago have very different IVP
    df.loc[580 - 576, "ivp_4h"] = 30.0  # delta_48h = +20 (>> 3, breaks C condition)
    df = add_pattern(df)
    assert df.loc[580, "pattern"] == "D"


def test_pattern_other_default():
    df = _synthetic_df(600)
    # Set ivp_4h=63 → not high enough for A/B (>70/>65), not low enough for C (<60).
    # adx=15 → below D threshold (>25). Result should be "Other".
    df.loc[:, "ivp_4h"] = 63.0
    df = add_pattern(df)
    assert df.loc[580, "pattern"] == "Other"


def test_pattern_priority_a_overrides_d():
    """When both A and D conditions are met, A wins."""
    df = _synthetic_df(600)
    df.loc[580, "ivp_4h"] = 75.0
    df.loc[580 - 288, "ivp_4h"] = 50.0  # delta_24h = +25 → A
    df.loc[580, "adx_14_4h"] = 30.0     # also satisfies D
    df = add_pattern(df)
    assert df.loc[580, "pattern"] == "A"


# ── Vol-of-vol ────────────────────────────────────────────────────────────────

def test_vol_of_vov_runs_without_error():
    """Smoke test: large enough series so daily resample produces values."""
    df = _synthetic_df(50 * 288)  # 50 days
    # Wiggle atm_iv_30d so daily diffs aren't zero
    np.random.seed(0)
    df["atm_iv_30d"] = 0.40 + np.random.normal(0, 0.005, len(df))
    df = add_vol_of_vol(df)
    assert "iv_change_stdev_7d" in df.columns
    assert "vov_ratio" in df.columns
    # After ~7 days of valid daily diffs, we should have non-NaN values
    assert df["iv_change_stdev_7d"].dropna().shape[0] > 0


def test_pattern_constants_values():
    """Sanity-check the pattern thresholds match the spec."""
    assert PATTERN_A_IVP_4H == 70
    assert PATTERN_A_DELTA_24H == 10
    assert PATTERN_B_IVP_4H == 65
    assert PATTERN_B_SPOT_RET_1D == -0.03
    assert PATTERN_C_IVP_4H == 60
    assert PATTERN_C_DELTA_48H == 3
    assert PATTERN_D_ADX_4H == 25
