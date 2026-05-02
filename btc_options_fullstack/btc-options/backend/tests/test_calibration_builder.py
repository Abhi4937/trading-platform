"""Unit tests for calibration_builder."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from app.analytics.calibration_builder import (
    DTE_BUCKETS,
    IVP_BUCKETS,
    SPOT_BUCKETS,
    _delta_label,
    _label_for_range,
    _spot_label,
    _snapshot_strike_marks,
    aggregate_buckets,
    build_universal_curve,
)


# ── Bucket labellers ──────────────────────────────────────────────────────────

def test_dte_label():
    assert _label_for_range(DTE_BUCKETS, 5) == "3-7"
    assert _label_for_range(DTE_BUCKETS, 0.5) == "0-3"
    assert _label_for_range(DTE_BUCKETS, 30) == "30-60"
    assert _label_for_range(DTE_BUCKETS, 90) == "nan"  # outside
    assert _label_for_range(DTE_BUCKETS, float("nan")) == "nan"


def test_spot_label():
    assert _spot_label(95_000) == "90-120k"
    assert _spot_label(50_000) == "0-60k"
    assert _spot_label(170_000) == "150k+"
    assert _spot_label(60_000) == "60-90k"  # left-inclusive


def test_ivp_label():
    assert _label_for_range(IVP_BUCKETS, 75) == "60-80"
    assert _label_for_range(IVP_BUCKETS, 0) == "0-20"
    assert _label_for_range(IVP_BUCKETS, 100) == "80-100"


def test_delta_label_snaps_to_standard():
    assert _delta_label(0.07) == "0.05"   # closer to 0.05 than 0.10
    assert _delta_label(0.10) == "0.10"
    assert _delta_label(0.13) == "0.15"   # closer to 0.15
    assert _delta_label(0.21) == "0.25"   # closer to 0.25 than 0.15? abs(0.21-0.15)=0.06 vs abs(0.21-0.25)=0.04 → 0.25


# ── Snapshot strike picker ────────────────────────────────────────────────────

def _synthetic_chain(strikes_marks_ce_pe: list[tuple[int, float, float]]) -> pd.DataFrame:
    """Build a chain DataFrame with the (strike, ce_mark, pe_mark) tuples given."""
    rows = []
    for k, ce, pe in strikes_marks_ce_pe:
        rows.append({"timestamp_unix": 1700000000, "strike": k,
                     "opt_type": "CE", "mark_close": ce, "oi_close": 0.0})
        rows.append({"timestamp_unix": 1700000000, "strike": k,
                     "opt_type": "PE", "mark_close": pe, "oi_close": 0.0})
    return pd.DataFrame(rows)


def test_snapshot_strike_marks_picks_closest_delta():
    """At spot=100k, T=7/365, build chain with known marks and pick 0.10Δ."""
    spot = 100_000.0
    T = 7 / 365.0

    # Synthetic chain: a ladder of strikes with marks chosen so that delta varies.
    # We can't easily back-solve which strike gets which delta without knowing
    # IV, so we just check: function returns a valid pick (not all NaN).
    chain = _synthetic_chain([
        (90_000, 10_500, 100),    # deep ITM call / far OTM put
        (95_000, 5_800, 400),
        (100_000, 2_500, 1_500),  # ATM
        (105_000, 800, 4_200),
        (110_000, 200, 8_500),    # far OTM call / deep ITM put
    ])
    picks = _snapshot_strike_marks(chain, spot, T, target_deltas=(0.10, 0.25))
    for td in (0.10, 0.25):
        assert td in picks
        # Should pick *something* (not all NaN) when chain is well-formed
        assert not math.isnan(picks[td]["call_strike"])
        assert not math.isnan(picks[td]["put_strike"])
        assert picks[td]["call_mark"] > 0
        assert picks[td]["put_mark"] > 0


def test_snapshot_strike_marks_empty_chain_returns_nan():
    picks = _snapshot_strike_marks(pd.DataFrame(), 100_000.0, 7 / 365.0, (0.10,))
    assert math.isnan(picks[0.10]["call_mark"])
    assert math.isnan(picks[0.10]["put_mark"])


# ── Aggregation ───────────────────────────────────────────────────────────────

def _synthetic_raw(n_per_bucket: int = 50) -> pd.DataFrame:
    """Build a synthetic 'raw' DataFrame with controlled bucket fillings."""
    rng = np.random.default_rng(42)
    rows = []
    # One dense bucket: dte=10, spot=100k, delta=0.10, ivp=70 (60-80 IVP bucket)
    for _ in range(n_per_bucket):
        rows.append({
            "ts_unix": 1700000000,
            "expiry_unix": 1700604800,
            "expiry_date": "2023-11-21",
            "dte": 10,
            "spot": 100_000.0 + rng.normal(0, 1000),
            "target_delta": 0.10,
            "call_strike": 110_000, "put_strike": 90_000,
            "call_mark": 200, "put_mark": 150,
            "total_premium": 350,
            "credit_pct": 0.0035 + rng.normal(0, 0.0005),
            "credit_pct_normalized": 0.0035 / math.sqrt(10),
            "call_iv": 0.60, "put_iv": 0.65,
            "call_delta": 0.10, "put_delta": -0.10,
            "strangle_iv_avg": 0.625,
            "atm_iv_7d": 0.55, "atm_iv_14d": 0.52, "atm_iv_30d": 0.50, "atm_iv_60d": 0.49,
            "ivp_atm_7d_90d": 70.0, "ivp_atm_14d_90d": 65.0, "ivp_atm_30d_90d": 60.0,
            "ivp_4h": 70.0,
            "rv_7d": 35.0, "rv_14d": 36.0, "rv_30d": 38.0,
            "iv_rv_spread_7d": 0.20, "iv_rv_spread_30d": 0.12, "iv_rv_ratio_7d": 1.57,
            "vrp_pct_7d": 75.0,
            "risk_reversal_25d": -0.05, "butterfly_25d": 0.04, "wing_atm_ratio": 1.10,
            "term_slope_7_30": -0.05,
            "rvp_4h": 50.0,
            "adx_14_4h": 18.0, "atr_pct_4h": 0.012,
            "pcr_oi": 0.55, "total_gex": -2.5e8, "gex_regime": "NEGATIVE",
            "pattern": "A",
        })
    # Filling for a structural bucket too — same dte/spot/delta but ivp=50 (40-60)
    for _ in range(n_per_bucket):
        rows.append({
            **rows[0],
            "credit_pct": 0.0028 + rng.normal(0, 0.0003),
            "credit_pct_normalized": 0.0028 / math.sqrt(10),
            "ivp_atm_7d_90d": 50.0,
            "pattern": "C",
        })
    return pd.DataFrame(rows)


def test_aggregate_buckets_stats():
    raw = _synthetic_raw(n_per_bucket=50)
    agg = aggregate_buckets(raw)

    # Should have 2 buckets (one for IVP=60-80, one for IVP=40-60)
    assert len(agg) == 2
    assert set(agg["ivp_bucket"]) == {"60-80", "40-60"}
    high = agg[agg["ivp_bucket"] == "60-80"].iloc[0]
    low = agg[agg["ivp_bucket"] == "40-60"].iloc[0]
    assert high["n_samples"] == 50
    assert low["n_samples"] == 50
    # High-IVP bucket should have higher median credit_pct
    assert high["credit_pct_median"] > low["credit_pct_median"]
    # Structural baseline should be the IVP=40-60 bucket's median
    assert abs(high["structural_baseline"] - low["credit_pct_median"]) < 1e-6


def test_universal_curve_has_one_row_per_delta_ivp():
    raw = _synthetic_raw(n_per_bucket=50)
    universal = build_universal_curve(raw)
    # Two IVP buckets × 1 delta_target = 2 rows
    assert len(universal) == 2
    assert set(universal.columns) >= {
        "delta_target", "ivp_bucket", "credit_pct_normalized_median",
        "credit_pct_normalized_mean", "credit_pct_normalized_std",
    }
