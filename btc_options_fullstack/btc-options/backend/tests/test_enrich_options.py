"""Unit tests for Module 2 options enrichment.

Targets the math + edge cases without touching the parquet pipeline:
- Vectorized BS price + IV solver round-trip
- Vectorized gamma
- Constant-maturity interpolation
- GEX regime classification
- IST timestamp conversion
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from app.analytics.enrich_options import (
    GEX_NEAR_FLIP_THRESHOLD_USD,
    _gex_regime,
    _interp_atm_iv,
    _ts_to_ist,
    bs_price_vec,
    gammas_vec,
    implied_vol_vec,
)
from app.core.greeks import implied_vol


# ── BS pricing + IV solver round-trip ─────────────────────────────────────────

def test_bs_price_vec_call_atm_roughly_correct():
    """At ATM with σ=0.5, T=30/365, the BS call price should be a known order."""
    S = 100.0
    K = np.array([100.0])
    sigma = np.array([0.5])
    is_call = np.array([True])
    p = bs_price_vec(S, K, 30 / 365, 0.0, sigma, is_call)
    # 30-day ATM call at 50% vol on $100 spot ≈ $5.7-$6.0
    assert 5.5 < p[0] < 6.5


def test_implied_vol_vec_round_trip_bs():
    """Generate prices via bs_price_vec then solve IV — should recover σ within 0.01."""
    S = 100.0
    K = np.array([90.0, 95.0, 100.0, 105.0, 110.0])
    sigmas = np.array([0.30, 0.45, 0.55, 0.50, 0.35])
    is_call = np.array([True, True, True, True, True])
    T = 30 / 365
    prices = bs_price_vec(S, K, T, 0.0, sigmas, is_call)
    recovered = implied_vol_vec(prices, S, K, T, 0.0, is_call, n_iter=24)
    np.testing.assert_allclose(recovered, sigmas, atol=0.01)


def test_implied_vol_vec_floor_for_intrinsic():
    """Sub-intrinsic price returns floor (0.0001), not NaN/error."""
    S = 100.0
    K = np.array([50.0])
    is_call = np.array([True])
    T = 7 / 365
    market = np.array([0.5])  # below intrinsic of 50
    iv = implied_vol_vec(market, S, K, T, 0.0, is_call)
    assert math.isclose(iv[0], 0.0001, rel_tol=1e-3)


def test_implied_vol_vec_matches_scalar_solver():
    """Vectorized solver should match the scalar greeks.implied_vol() within tolerance."""
    S = 75000.0
    K = np.array([74000.0, 75000.0, 76000.0])
    is_call = np.array([True, True, False])
    T = 14 / 365
    market_prices = np.array([1500.0, 1100.0, 950.0])
    iv_vec = implied_vol_vec(market_prices, S, K, T, 0.0, is_call, n_iter=24)
    iv_scalar = np.array([
        implied_vol(market_prices[i], S, float(K[i]), T, 0.0, "call" if is_call[i] else "put")
        for i in range(3)
    ])
    np.testing.assert_allclose(iv_vec, iv_scalar, atol=0.01)


# ── Vectorized gamma ──────────────────────────────────────────────────────────

def test_gammas_vec_atm_max():
    """ATM gamma is highest among adjacent strikes for short-dated options."""
    S = 100.0
    Ks = np.array([90.0, 95.0, 100.0, 105.0, 110.0])
    sigmas = np.full_like(Ks, 0.5)
    g = gammas_vec(S, Ks, 7 / 365, sigmas)
    # ATM (K=100) should have the largest gamma
    assert int(np.argmax(g)) == 2  # index of K=100


def test_gammas_vec_zero_for_degenerate():
    """T=0 or σ=0 returns zero gamma."""
    g = gammas_vec(100.0, np.array([100.0]), 0.0, np.array([0.5]))
    assert g[0] == 0.0
    g = gammas_vec(100.0, np.array([100.0]), 7 / 365, np.array([0.0]))
    assert g[0] == 0.0


# ── Constant-maturity interpolation ───────────────────────────────────────────

def test_interp_atm_iv_linear():
    """Linear interp between (5d, 0.5) and (14d, 0.4) → 7d ≈ 0.4778."""
    target = 7.0
    result = _interp_atm_iv(target, [5.0, 14.0], [0.5, 0.4])
    expected = 0.5 + (0.4 - 0.5) * (7 - 5) / (14 - 5)  # 0.4778
    assert math.isclose(result, expected, rel_tol=1e-6)


def test_interp_atm_iv_outside_range_nan():
    """Target outside [min, max] dte returns NaN — no extrapolation."""
    # Target 60 with only [5, 14] expiries
    result = _interp_atm_iv(60.0, [5.0, 14.0], [0.5, 0.4])
    assert math.isnan(result)
    # Target 1 — also below min
    result = _interp_atm_iv(1.0, [5.0, 14.0], [0.5, 0.4])
    assert math.isnan(result)


def test_interp_atm_iv_exact_match():
    """Target equals one of the dtes → returns that dte's IV exactly."""
    result = _interp_atm_iv(5.0, [5.0, 14.0], [0.5, 0.4])
    assert math.isclose(result, 0.5, rel_tol=1e-9)


def test_interp_atm_iv_handles_nan_inputs():
    """NaN IVs are filtered out before interpolation."""
    result = _interp_atm_iv(7.0, [5.0, 10.0, 14.0], [0.5, float("nan"), 0.4])
    expected = 0.5 + (0.4 - 0.5) * (7 - 5) / (14 - 5)
    assert math.isclose(result, expected, rel_tol=1e-6)


# ── GEX regime ────────────────────────────────────────────────────────────────

def test_gex_regime_positive():
    assert _gex_regime(100_000_000, float("nan")) == "POSITIVE"


def test_gex_regime_negative():
    assert _gex_regime(-100_000_000, float("nan")) == "NEGATIVE"


def test_gex_regime_near_flip_below_threshold():
    half = GEX_NEAR_FLIP_THRESHOLD_USD * 0.5
    assert _gex_regime(half, float("nan")) == "NEAR_FLIP"
    assert _gex_regime(-half, float("nan")) == "NEAR_FLIP"


def test_gex_regime_nan():
    assert _gex_regime(float("nan"), float("nan")) == ""


# ── Timestamp conversion ──────────────────────────────────────────────────────

def test_ts_to_ist_known_unix():
    """Verify UNIX → IST naive conversion against a known timestamp."""
    # 2025-01-01 12:00 UTC = 2025-01-01 17:30 IST
    ts = pd.Series([1735732800])
    out = _ts_to_ist(ts)
    # `_ts_to_ist` returns a DatetimeIndex; first element is a Timestamp.
    assert out[0].strftime("%Y-%m-%d %H:%M") == "2025-01-01 17:30"
