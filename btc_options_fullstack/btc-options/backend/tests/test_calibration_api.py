"""Tests for /historical/calibration and /historical/snapshot-context."""

from __future__ import annotations

import json
import os
import tempfile

import pandas as pd
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client_with_calibration(tmp_path, monkeypatch):
    """Create a TestClient with a synthetic calibration parquet stubbed in."""
    # Build a minimal calibration table
    rows = [
        # Specific bucket with n>=30
        {
            "dte_bucket": "7-14", "spot_bucket": "90-120k",
            "delta_target": "0.10", "ivp_bucket": "60-80",
            "n_samples": 142,
            "credit_pct_median": 0.0058, "credit_pct_mean": 0.0061,
            "credit_pct_std": 0.0014, "credit_pct_p25": 0.0050, "credit_pct_p75": 0.0070,
            "credit_pct_normalized_median": 0.0019,
            "atm_iv_median": 0.42, "atm_iv_mean": 0.43, "atm_iv_std": 0.06,
            "strangle_iv_median": 0.45, "strangle_iv_std": 0.08,
            "risk_reversal_25d_median": -0.05, "butterfly_25d_median": 0.04,
            "term_slope_7_30_median": -0.02, "iv_rv_spread_7d_median": 0.10,
            "pcr_oi_median": 0.55, "total_gex_median": -2.0e8,
            "adx_14_4h_median": 18.0, "atr_pct_4h_median": 0.012,
            "structural_baseline": 0.0048,
            "pattern_distribution": json.dumps({"A": 0.2, "B": 0.1, "C": 0.3, "D": 0.2, "Other": 0.2}),
        },
        # Sparse bucket: n<30 → should fall back to universal
        {
            "dte_bucket": "30-60", "spot_bucket": "150k+",
            "delta_target": "0.05", "ivp_bucket": "0-20",
            "n_samples": 5,
            "credit_pct_median": 0.0010, "credit_pct_mean": 0.0010,
            "credit_pct_std": 0.0002, "credit_pct_p25": 0.0009, "credit_pct_p75": 0.0011,
            "credit_pct_normalized_median": 0.00018,
            "atm_iv_median": 0.30, "atm_iv_mean": 0.30, "atm_iv_std": 0.05,
            "strangle_iv_median": 0.32, "strangle_iv_std": 0.06,
            "risk_reversal_25d_median": -0.03, "butterfly_25d_median": 0.02,
            "term_slope_7_30_median": 0.00, "iv_rv_spread_7d_median": 0.08,
            "pcr_oi_median": 0.50, "total_gex_median": -1.0e8,
            "adx_14_4h_median": 15.0, "atr_pct_4h_median": 0.010,
            "structural_baseline": 0.0009,
            "pattern_distribution": json.dumps({"C": 0.6, "Other": 0.4}),
        },
    ]
    calib_path = tmp_path / "calibration.parquet"
    pd.DataFrame(rows).to_parquet(calib_path, compression="snappy", index=False)

    # Universal curve
    universal = pd.DataFrame([
        {"delta_target": "0.10", "ivp_bucket": "60-80", "n_samples": 200,
         "credit_pct_normalized_median": 0.0020, "credit_pct_normalized_mean": 0.0021,
         "credit_pct_normalized_std": 0.0005, "atm_iv_median": 0.40, "atm_iv_std": 0.07},
        {"delta_target": "0.10", "ivp_bucket": "40-60", "n_samples": 200,
         "credit_pct_normalized_median": 0.0016, "credit_pct_normalized_mean": 0.0017,
         "credit_pct_normalized_std": 0.0004, "atm_iv_median": 0.36, "atm_iv_std": 0.06},
        {"delta_target": "0.05", "ivp_bucket": "0-20", "n_samples": 200,
         "credit_pct_normalized_median": 0.00015, "credit_pct_normalized_mean": 0.00016,
         "credit_pct_normalized_std": 0.00004, "atm_iv_median": 0.28, "atm_iv_std": 0.05},
        {"delta_target": "0.05", "ivp_bucket": "40-60", "n_samples": 200,
         "credit_pct_normalized_median": 0.00020, "credit_pct_normalized_mean": 0.00021,
         "credit_pct_normalized_std": 0.00005, "atm_iv_median": 0.32, "atm_iv_std": 0.06},
    ])
    univ_path = tmp_path / "calibration_universal.parquet"
    universal.to_parquet(univ_path, compression="snappy", index=False)

    # Patch the module's path constants
    from app.api import historical
    monkeypatch.setattr(historical, "CALIBRATION_PATH", str(calib_path))
    monkeypatch.setattr(historical, "CALIBRATION_UNIVERSAL_PATH", str(univ_path))
    # Reset cache
    historical._calibration_cache.clear()
    historical._calibration_loaded["specific"] = None
    historical._calibration_loaded["universal"] = None

    from app.main import app
    return TestClient(app)


def test_calibration_specific_bucket(client_with_calibration):
    r = client_with_calibration.get(
        "/api/v1/historical/calibration",
        params={"dte": 10, "spot": 100_000, "delta_target": 0.10, "ivp": 70},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["source"] == "specific_bucket"
    assert data["n_samples"] == 142
    assert abs(data["credit_pct_median"] - 0.0058) < 1e-9
    assert data["bucket"] == {
        "dte_bucket": "7-14", "spot_bucket": "90-120k",
        "delta_target": "0.10", "ivp_bucket": "60-80",
    }


def test_calibration_universal_fallback_for_sparse(client_with_calibration):
    """A bucket with n<30 should fall back to universal."""
    r = client_with_calibration.get(
        "/api/v1/historical/calibration",
        params={"dte": 45, "spot": 175_000, "delta_target": 0.05, "ivp": 10},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["source"] == "universal_fallback"
    # universal_median (0.00015) × sqrt(45) ≈ 0.00100
    assert 0.0009 < data["credit_pct_median"] < 0.0012


def test_calibration_404_when_no_match(client_with_calibration):
    """Requesting a delta+IVP combination not in either table → 404."""
    r = client_with_calibration.get(
        "/api/v1/historical/calibration",
        params={"dte": 7, "spot": 100_000, "delta_target": 0.25, "ivp": 95},
    )
    assert r.status_code == 404


def test_calibration_503_when_not_built(monkeypatch):
    """If parquets don't exist, return 503."""
    from app.api import historical
    monkeypatch.setattr(historical, "CALIBRATION_PATH", "/nonexistent/calib.parquet")
    monkeypatch.setattr(historical, "CALIBRATION_UNIVERSAL_PATH", "/nonexistent/universal.parquet")
    historical._calibration_cache.clear()
    historical._calibration_loaded["specific"] = None
    historical._calibration_loaded["universal"] = None

    from app.main import app
    client = TestClient(app)
    r = client.get("/api/v1/historical/calibration",
                   params={"dte": 7, "spot": 100_000, "delta_target": 0.10, "ivp": 70})
    assert r.status_code == 503
